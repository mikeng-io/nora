"""Conformance: today's concrete classes must satisfy the ports they'll be injected as.

This is what makes the ports real rather than aspirational. Where a class does NOT
yet conform, the gap is asserted explicitly with the phase that closes it — so the
divergence is tracked, not forgotten.
"""

import inspect

from nora.domain.ports import MemoryStore
from nora.infra.honcho import HonchoClient
from nora.platform.signal.adapter import SignalAdapter
from nora.testing import FakeMemoryStore


def test_honcho_client_satisfies_memory_store_method_names():
    for method in ("ensure_workspace", "ensure_peer", "feed", "peer_card", "health", "aclose"):
        assert callable(getattr(HonchoClient, method, None)), f"HonchoClient lacks {method}"


def test_fake_memory_store_satisfies_memory_store():
    """`FakeMemoryStore` (`nora/testing/fakes.py`) is `MemoryStore`'s second
    implementation — `tests/integration/harness.py` and `evals/harness.py` both
    build their pipeline against it instead of the real, networked `HonchoClient`.
    `isinstance` against a `runtime_checkable` `Protocol` checks method names
    only (see `test_runtime_checkable_protocols_only_check_method_names` below);
    unlike `HonchoClient`, `FakeMemoryStore` is also keyword-only exactly like the
    port, so it does not carry `tests/fakes.py::FakeHoncho`'s older
    positional-argument drift."""
    assert isinstance(FakeMemoryStore(), MemoryStore)


def test_signal_adapter_resolve_identity_satisfies_the_port_shape():
    """Replaces `test_signal_adapter_gap_to_platform_adapter_is_known`, which pinned
    `resolve_identity()` as absent — the platforms phase adds it, so this is the
    positive assertion in its place. `send()`'s SendResult/keyword-only shape and
    `health()`'s AdapterHealth shape are asserted by their own tests, added
    alongside the tasks that close those two gaps."""
    assert hasattr(SignalAdapter, "resolve_identity")
    identity_sig = inspect.signature(SignalAdapter.resolve_identity)
    assert identity_sig.return_annotation == "PlatformIdentity"


def test_signal_adapter_send_satisfies_the_port_shape():
    """`send()` becomes keyword-only-after-self and returns `SendResult` instead
    of a positional-target bare `bool` — the platforms phase's second gap close."""
    send_params = inspect.signature(SignalAdapter.send).parameters
    assert send_params["target"].kind is inspect.Parameter.KEYWORD_ONLY
    assert send_params["text"].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(SignalAdapter.send).return_annotation == "SendResult"


def test_signal_adapter_health_gap_to_adapter_health_is_known():
    """SignalAdapter.health() does NOT yet satisfy PlatformAdapter, and that is
    expected: it returns a plain dict where the port declares -> AdapterHealth.
    Pinning the gap keeps it visible instead of letting it drift."""
    return_annotation = inspect.signature(SignalAdapter.health).return_annotation
    assert return_annotation == "dict", (
        "SignalAdapter.health() no longer returns a bare dict — update this test and "
        "ARCHITECTURE.md's PlatformAdapter known-gap row"
    )


def test_signal_adapter_listen_gap_to_on_message_is_known():
    """SignalAdapter.listen() does NOT yet satisfy PlatformAdapter, and that is
    expected: it takes a positional on_event callback receiving a raw dict, where
    the port declares a keyword-only on_message: Callable[[NormalizedMessage],
    Awaitable[None]]. Pinning the gap keeps it visible instead of letting it drift."""
    params = inspect.signature(SignalAdapter.listen).parameters
    assert "on_event" in params and "on_message" not in params, (
        "SignalAdapter.listen() gained on_message — update this test and ARCHITECTURE.md's "
        "PlatformAdapter known-gap row"
    )
    assert params["on_event"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
        "SignalAdapter.listen()'s callback became keyword-only — update this test and "
        "ARCHITECTURE.md's PlatformAdapter known-gap row"
    )


def test_runtime_checkable_protocols_only_check_method_names():
    """A guard against false confidence: isinstance() against a runtime_checkable
    Protocol checks names, NOT signatures. Static checking (mypy) is what verifies
    the shapes — which is why DEC-12 adds it."""

    class Fake:
        async def ensure_workspace(self): ...
        async def ensure_peer(self, **kw): ...
        async def feed(self, **kw): ...
        async def peer_card(self, **kw): ...
        async def health(self): ...
        async def aclose(self): ...

    assert isinstance(Fake(), MemoryStore)
