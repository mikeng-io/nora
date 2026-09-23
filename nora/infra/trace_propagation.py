"""Trace-context propagation onto calls to Nora's OWN services.

Lives in `nora.infra`, not `nora.obs`: the layer contract puts `nora.obs` above
`nora.infra`, and both the platform adapters and the infra-level social client
call the bridge. It depends on nothing but OpenTelemetry's API.
"""

from __future__ import annotations

import logging

from opentelemetry import propagate

log = logging.getLogger("nora.infra.trace_propagation")


def propagation_headers() -> dict[str, str]:
    """W3C trace-context headers for the CURRENT span, to put on a call to one
    of Nora's OWN services (instagram-bridge) so that service's spans join this
    trace instead of starting a new one (design/instagram-adoption/16
    §Tracing). Before this existed every bridge call was its own unlinked
    trace.

    **Only for Nora's own services, never a third party.** A `traceparent`
    sent to an external host hands it an identifier of this infrastructure;
    the Instagram CDN fetch in `nora.platform.instagram.media` deliberately
    does not use this.

    Never raises: `{}` is the degraded answer, and an unpropagated call is
    simply a separate trace.
    """
    carrier: dict[str, str] = {}
    try:
        propagate.inject(carrier)
    except Exception:  # a broken propagator must not break a send
        log.debug("trace context could not be propagated", exc_info=True)
        return {}
    return carrier
