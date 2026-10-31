"""Synthetic Langfuse — an OTLP/HTTP JSON trace endpoint that REMEMBERS.

The fixture's collector routes its tail-sampled (generation-only) pipeline
here as `otlp_http` with `encoding: json`; this stands in for Langfuse's
ingest so a test can read back EXACTLY which spans that pipeline released —
the positive control for "queue-only traces never reach Langfuse". It keeps
each span's name, trace/span ids, kind and attribute map. No auth, no
persistence, no paid anything — the port is loopback-only in compose.

Endpoints:
    POST /v1/traces   OTLP/JSON ExportTraceServiceRequest — stores spans
    GET  /spans       {"spans": [...]} — everything accepted so far
    POST /reset       clears the store (returns {"cleared": n})
    GET  /healthz     "ok"
"""

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOCK = threading.Lock()
_SPANS: list[dict] = []


def _attr_value(v: dict):
    """OTLP/JSON AnyValue → a plain python scalar."""
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    if "arrayValue" in v:
        return [_attr_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def _store(payload: dict) -> int:
    kept = 0
    for rs in payload.get("resourceSpans", []):
        service = ""
        for a in rs.get("resource", {}).get("attributes", []):
            if a.get("key") == "service.name":
                service = _attr_value(a.get("value", {}))
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                attrs = {a["key"]: _attr_value(a.get("value", {}))
                         for a in span.get("attributes", [])
                         if "key" in a}
                with _LOCK:
                    _SPANS.append({
                        "service": service,
                        "traceId": span.get("traceId"),
                        "spanId": span.get("spanId"),
                        "parentSpanId": span.get("parentSpanId"),
                        "name": span.get("name"),
                        "kind": span.get("kind"),
                        "attributes": attrs,
                    })
                kept += 1
    return kept


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code: int, body: bytes,
               ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self._reply(200, b"ok", "text/plain")
        if self.path == "/spans":
            with _LOCK:
                body = json.dumps({"spans": list(_SPANS)}).encode()
            return self._reply(200, body)
        self._reply(404, b"{}")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        if self.path == "/reset":
            with _LOCK:
                n = len(_SPANS)
                _SPANS.clear()
            return self._reply(200, json.dumps({"cleared": n}).encode())
        if self.path == "/v1/traces":
            try:
                kept = _store(json.loads(raw or b"{}"))
            # a bad body is a bad request, not a crash
            except (json.JSONDecodeError, KeyError, TypeError,
                    AttributeError, ValueError) as exc:
                return self._reply(400,
                                   json.dumps({"error": str(exc)}).encode())
            # OTLP/JSON's success shape.
            return self._reply(200, json.dumps({"partialSuccess": {},
                                               "received": kept}).encode())
        self._reply(404, b"{}")

    def log_message(self, fmt: str, *args) -> None:  # quiet: fixture logs are noise
        pass


if __name__ == "__main__":
    # In-compose listener — the loopback-only host port is in compose.
    ThreadingHTTPServer(("0.0.0.0", 4318), Handler).serve_forever()  # noqa: S104
