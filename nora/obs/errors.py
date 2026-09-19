"""Sentry, for the ONE question the rest of this package cannot answer.

**It is not a fourth store competing with the other three.** `docs/
observability.md` states the boundary: Prometheus answers "how many, how often,
with what outcome", Langfuse holds the traces with a model call in them, and
logs are forensic. All three answer questions about things that HAPPENED. None
of them groups two hundred occurrences of the same stack trace into one issue —
which is the one thing this module exists for. It does NOT currently tell you
an issue is new since the last deploy (no release is stamped — see
`setup_errors`'s `release` parameter), and as of `include_local_variables=False`
below it deliberately does not put a frame's local variables in front of you
either — Prometheus and Langfuse have their own gaps this module does not
close.

**And it is deliberately errors ONLY — `traces_sample_rate` is 0.**
`sentry_sdk` will happily instrument spans, and letting it would give this
process two tracing pipelines writing two incompatible trace ids for the same
turn, which is worse than one: a trace that exists twice, differently, cannot
be re-derived from either copy. Tracing belongs to `nora.obs.tracing` and lands
in Langfuse. Sentry gets exceptions.

**What it will and will not see, stated up front**, because assuming otherwise
is how a team believes it has alerting:

* it SEES the 223 `log.exception` sites — the `LoggingIntegration` captures a
  record logged at ERROR with `exc_info`, which is exactly the shape those
  take, plus anything that escapes to the top;
* it does NOT see an R-27 `unavailable`. This codebase is built not to raise —
  210 docstrings promise it, 45 metric specs declare an `unavailable` state —
  so its characteristic failure is a counter label, not a traceback. Nothing
  threw when the media read exported zero traces for two weeks, and nothing
  threw when `nora-egress.service` was dead for sixteen days. **Those are
  Prometheus' to catch**, and `deploy/prometheus/` is where that lives.

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

    The return value is not decoration: `__main__` logs it, so "Sentry is
    configured" is a line in the log rather than an assumption. A deployment
    that believes it has error alerting and does not is strictly worse than one
    that knows it has none.

    `dsn_env` NAMES an environment variable; the DSN is never inlined in
    `config.toml` (DEC-8), and an empty or unset value means OFF rather than an
    error — that is the shipped default, and a laptop running the test suite
    must not need a Sentry account.
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
        # An optional dependency that is not installed is a deployment choice,
        # not a crash — `sentry` is an extra, and the laptop running the test
        # suite does not have it. Narrow on purpose: this catches "not
        # installed", and an SDK that is present but broken on import is a
        # different problem that should be loud.
        log.warning("sentry: sentry_sdk is not installed; error reporting is off")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment or "unknown",
            release=release or None,
            # ERRORS ONLY. See the module docstring: a second tracing pipeline
            # would write a second, incompatible trace id for the same turn.
            traces_sample_rate=0.0,
            # `event_level=ERROR` is what turns this codebase's 223
            # `log.exception` sites into Sentry issues; `level=WARNING` is the
            # BREADCRUMB floor, so an issue arrives with the warnings that led
            # to it rather than alone.
            integrations=[
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            # AUTO-ENABLING INTEGRATIONS OFF, and this is not tidiness — with
            # them on, `init` RAISED and error reporting was silently off in
            # the deployment (2026-09-09):
            #
            #   ImportError: jinja2 must be installed to use Jinja2Templates
            #     sentry_sdk/integrations/starlette.py:550 in patch_templates
            #
            # The SDK scans for installed packages and wires an integration for
            # each one it recognises. `starlette` is in this image as a
            # transitive dependency of something Nora never serves a request
            # with, so the SDK reached for a framework integration whose own
            # optional dependency is absent — and a failure inside `init`
            # takes the whole reporter with it.
            #
            # We want exactly one integration and we pass it explicitly.
            # Everything the SDK would guess is a framework this process does
            # not use as a framework, so there is nothing to lose and a whole
            # class of import-time failure to avoid. `default_integrations`
            # stays ON: those are the SDK's own core (excepthook, dedupe,
            # atexit, stdlib) and are what make an unhandled exception an
            # event at all.
            auto_enabling_integrations=False,
            # `send_default_pii=False` does NOT keep a frame's local variable
            # VALUES off the wire — verified against the installed SDK
            # (sentry-sdk==2.44.0, `consts.py`): `include_local_variables`
            # defaults to `True` independently of `send_default_pii`, which
            # only gates broader request/user PII. An ACI or a phone number
            # sitting in a local when an exception fires is the same
            # identifier R-18 keeps off every other exit, so it is turned off
            # explicitly rather than assumed covered by the flag above.
            include_local_variables=False,
            send_default_pii=False,
            attach_stacktrace=True,
        )
    except Exception:
        log.warning("sentry: init failed; error reporting is off", exc_info=True)
        return False
    log.info("sentry: error reporting on (environment=%s)", environment or "unknown")
    return True
