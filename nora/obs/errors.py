"""Sentry, for the one question the rest of this package cannot answer.

`docs/observability.md` states the boundary: Prometheus answers "how many, how
often, with what outcome", Langfuse holds the traces with a model call in them,
and logs are forensic. All three answer questions about things that happened;
none groups many occurrences of the same stack trace into one issue, which is
what this module is for. It does not mark an issue as new since the last deploy
unless `setup_errors` is given a `release`, and it does not capture a frame's
local variables (`include_local_variables=False`).

**Errors only: `traces_sample_rate` is 0.** `sentry_sdk` can instrument spans,
but that would give this process two tracing pipelines writing two
incompatible trace ids for the same turn. Tracing belongs to `nora.obs.tracing`
and lands in Langfuse. Sentry gets exceptions.

What it will and will not see:

* it SEES every `log.exception` site: the `LoggingIntegration` captures a
  record logged at ERROR with `exc_info`, plus anything that escapes to the
  top;
* it does NOT see an R-27 `unavailable`. This codebase is built not to raise,
  so its characteristic failure is a counter label, not a traceback. Those are
  Prometheus' to catch (`deploy/prometheus/`).

Never load-bearing, like every other instrument here: a missing DSN, an
unreachable Sentry, an SDK that will not import — each costs error reporting
and nothing else.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("nora.errors")


def setup_errors(
    *,
    enabled: bool,
    dsn_env: str,
    environment: str = "",
    release: str = "",
) -> bool:
    """Wire Sentry for error capture. Returns whether it is ON.

    `__main__` logs the return value, so "Sentry is configured" is a line in
    the log rather than an assumption.

    `dsn_env` NAMES an environment variable; the DSN is never inlined in
    `config.toml` (DEC-8), and an empty or unset value means OFF rather than an
    error, so a test run needs no Sentry account.
    """
    if not enabled:
        return False
    dsn = os.environ.get(dsn_env, "").strip()
    if not dsn:
        log.info("sentry: %s is unset; error reporting is off", dsn_env)
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        # `sentry` is an optional extra; not installed is a deployment choice,
        # not a crash. Narrow on purpose: an SDK that is present but broken on
        # import should be loud.
        log.warning("sentry: sentry_sdk is not installed; error reporting is off")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment or "unknown",
            release=release or None,
            # Errors only; see the module docstring.
            traces_sample_rate=0.0,
            # `event_level=ERROR` turns `log.exception` sites into Sentry
            # issues; `level=WARNING` is the BREADCRUMB floor, so an issue
            # arrives with the warnings that led to it.
            integrations=[
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            # Auto-enabling integrations must stay off: the SDK wires one for
            # every package it finds (e.g. `starlette`, present here only as a
            # transitive dependency), and an integration whose own optional
            # dependency is absent (jinja2) makes `init` raise, which takes the
            # whole reporter with it. The one integration needed is passed
            # explicitly. `default_integrations` stays ON: those are the SDK's
            # core (excepthook, dedupe, atexit, stdlib) and make an unhandled
            # exception an event at all.
            auto_enabling_integrations=False,
            # `send_default_pii=False` does NOT keep a frame's local variable
            # values off the wire: `include_local_variables` defaults to
            # `True` independently of it. An ACI or a phone number in a local
            # is the identifier R-18 keeps off every other exit, so it is
            # turned off explicitly.
            include_local_variables=False,
            send_default_pii=False,
            attach_stacktrace=True,
        )
    except Exception:
        log.warning("sentry: init failed; error reporting is off", exc_info=True)
        return False
    log.info("sentry: error reporting on (environment=%s)", environment or "unknown")
    return True
