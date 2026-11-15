"""The one authorized live-model boundary for an evaluation case.

A `ModelGateway` declares the configured model API's upstream and the
subject/judge identities a dispatch may name. `IsolatedServices` resolves it
at open and then admits requests to it one at a time: destination, method,
the configured API route and the body's declared model are checked at the
HTTP layer, and the dispatch must run through the installed `RunBudget`'s
bounded transport so it is counted exactly once. The upstream's pinned
addresses are reachable only while that admitted dispatch is in flight;
every other external effect stays refused. Missing declaration,
authorization or resolution is refused — there is no live default.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class ModelGateway:
    """A declared live-model boundary: one upstream and its named identities.

    `upstream` is the configured model API base URL (origin plus base path,
    e.g. "https://gateway.internal/v1"); `models` holds the subject/judge
    identities a dispatch may name — a body whose `model` names anything
    else is refused before external dispatch. Aliases are never rewritten.
    """

    upstream: str
    models: frozenset[str]

    def __post_init__(self) -> None:
        parts = urlsplit(self.upstream)
        if (parts.scheme not in _PORTS or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError("model gateway requires a plain http(s) upstream")
        if parts.port is not None and not (0 < parts.port < 65536):
            raise ValueError("model gateway upstream has a malformed port")
        if not self.models or not all(
                isinstance(model, str) and model for model in self.models):
            raise ValueError("model gateway requires declared model identities")

    def resolve(self) -> ResolvedGateway:
        """Pin the declared upstream's addresses before the process guard installs."""
        parts = urlsplit(self.upstream)
        host = (parts.hostname or "").lower()
        port = parts.port or _PORTS[parts.scheme]
        addresses = frozenset(
            (str(item[4][0]), port)
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        if not addresses:
            raise ValueError(
                f"model gateway upstream resolves to no address: {self.upstream}")
        return ResolvedGateway(
            scheme=parts.scheme, host=host, port=port,
            route=parts.path.rstrip("/") + "/chat/completions",
            models=self.models, addresses=addresses)


@dataclass(frozen=True, slots=True)
class ResolvedGateway:
    """A `ModelGateway` pinned against real addresses at environment open."""

    scheme: str
    host: str
    port: int
    route: str
    models: frozenset[str]
    addresses: frozenset[tuple[str, int]]

    def refusal(self, client: Any, request: Any, raw: bytes | None) -> str | None:
        """A refusal cause for this dispatch, or None when it may proceed.

        `raw` is the request's already-serialized body; `client` is the
        httpx-level client, whose transport must be the installed budget's
        `_BoundedTransport` — the construction that counts this dispatch
        exactly once through `RunBudget`.
        """
        url = urlsplit(str(request.url))
        port = url.port or _PORTS.get(url.scheme, 0)
        if (url.scheme, (url.hostname or "").lower(), port) != (
                self.scheme, self.host, self.port):
            return "external_dependency_not_isolated"
        if request.method != "POST":
            return "model_method_undeclared"
        if url.path != self.route:
            return "model_path_undeclared"
        body = None
        if raw is not None:
            try:
                body = json.loads(raw)
            except (ValueError, TypeError):
                body = None
        if not isinstance(body, dict) or body.get("model") not in self.models:
            return "model_identity_undeclared"
        from nora.domain.run_budget import active_run_budget

        budget = active_run_budget.get()
        if budget is None or getattr(
                getattr(client, "_transport", None), "_budget", None) is not budget:
            return "model_dispatch_unbudgeted"
        return None
