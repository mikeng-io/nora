"""Conformance: today's concrete classes must satisfy the ports they'll be injected as.

This is what makes the ports real rather than aspirational. Where a class does NOT
yet conform, the gap is asserted explicitly with the phase that closes it — so the
divergence is tracked, not forgotten.
"""

from nora.domain.ports import MemoryStore
from nora.platform.signal.adapter import SignalAdapter
from nora.store.honcho import HonchoClient


def test_honcho_client_satisfies_memory_store_method_names():
    for method in ("ensure_workspace", "ensure_peer", "feed", "peer_card", "health", "aclose"):
        assert callable(getattr(HonchoClient, method, None)), f"HonchoClient lacks {method}"


def test_signal_adapter_gap_to_platform_adapter_is_known():
    """SignalAdapter does NOT yet satisfy PlatformAdapter, and that is expected:
    resolve_identity() and the SendResult return type land in the platforms phase.
    Pinning the gap keeps it visible instead of letting it drift."""
    assert hasattr(SignalAdapter, "listen") and hasattr(SignalAdapter, "send")
    assert not hasattr(SignalAdapter, "resolve_identity"), (
        "SignalAdapter grew resolve_identity — update this test and the ports phase note"
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
