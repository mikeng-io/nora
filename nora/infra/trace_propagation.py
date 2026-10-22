"""Trace-context propagation onto calls to Nora's OWN services.

Lives in `nora.infra`, not `nora.obs`: the layer contract puts `nora.obs` above
`nora.infra`, and both the platform adapters and the infra-level social client
call the bridge. It depends on OpenTelemetry's API and the shared service-token
helper, nothing else.
"""

from __future__ import annotations

import logging

from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .service_auth import service_auth_headers

log = logging.getLogger("nora.infra.trace_propagation")

#: W3C Trace Context, explicitly, not the global propagator. The bridge
#: extracts W3C `traceparent`; that header IS the contract, so it is named here
#: rather than inherited from whatever `OTEL_PROPAGATORS` or a later
#: `set_global_textmap` call happens to install.
_W3C = TraceContextTextMapPropagator()


def propagation_headers() -> dict[str, str]:
    """W3C trace-context headers for the CURRENT span, to put on a call to one
    of Nora's OWN services (instagram-bridge) so that service's spans join this
    trace instead of starting a new one.

    **Only for Nora's own services, never a third party.** A `traceparent`
    sent to an external host hands it an identifier of this infrastructure;
    the Instagram CDN fetch in `nora.platform.instagram.media` deliberately
    does not use this.

    Never raises: `{}` is the degraded answer, and an unpropagated call is
    simply a separate trace.
    """
    carrier: dict[str, str] = {}
    try:
        _W3C.inject(carrier)
    except Exception:  # a broken propagator must not break a send
        log.debug("trace context could not be propagated", exc_info=True)
        carrier = {}
    # The same helper is the one place a call to Nora's own bridge learns the
    # shared token, so a new call site cannot forget it.
    return {**carrier, **service_auth_headers()}
