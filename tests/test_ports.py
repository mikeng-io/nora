"""Ports are structural contracts. These tests pin the shape later phases build against."""

import inspect

from nora.domain.ports import (
    AdapterHealth,
    AuditStore,
    ContextAssembler,
    EngageDecision,
    EngagePolicy,
    MemoryStore,
    ModelTier,
    PlatformAdapter,
    PlatformIdentity,
    SendResult,
)


def test_platform_identity_holds_aliases_not_one_id():
    """signal-cli emits `source` OR `sourceNumber` and sometimes a UUID; Telegram has
    a numeric id AND a username. R-12 (ignore self-triggers) must test membership,
    not equality, or it silently stops working on the second platform."""
    ident = PlatformIdentity(platform="signal", primary="+85290000100",
                             aliases=frozenset({"+85290000100", "uuid-abc"}))
    assert "uuid-abc" in ident.aliases
    assert ident.primary in ident.aliases


def test_send_result_can_express_a_retryable_failure():
    """Telegram answers 429 with retry_after. Deliverer's latch counts ATTEMPTS, so a
    bare bool would burn the single send on the most common transient failure."""
    ok = SendResult(ok=True)
    assert ok.ok and not ok.retryable
    rate_limited = SendResult(ok=False, retryable=True, retry_after=30.0)
    assert not rate_limited.ok and rate_limited.retryable and rate_limited.retry_after == 30.0


def test_engage_decision_carries_the_rationale_and_threshold():
    """design/02 requires the classifier's reason to reach the prefill, and a bare
    confidence is uninterpretable without the threshold it was compared against."""
    d = EngageDecision(engage=True, reason="trigger_text", tier=ModelTier.FLASH)
    assert d.engage and d.rationale is None and d.confidence is None
    c = EngageDecision(engage=True, reason="classifier", tier=ModelTier.DEEP,
                       rationale="asked a factual question about dinner",
                       confidence=0.82, threshold=0.7, allow_episode=True)
    assert c.confidence > c.threshold and c.allow_episode


def test_ports_are_runtime_checkable_protocols():
    for port in (PlatformAdapter, AuditStore, MemoryStore, EngagePolicy, ContextAssembler):
        assert getattr(port, "_is_protocol", False), f"{port.__name__} is not a Protocol"


def test_port_methods_are_keyword_only_after_self():
    """Convention from spec §4.2: free extensibility forever. A positional parameter
    added to a port later is a breaking change at every call site."""
    offenders = []
    for port in (PlatformAdapter, AuditStore, MemoryStore, EngagePolicy, ContextAssembler):
        for name, member in vars(port).items():
            if name.startswith("_") or not callable(member):
                continue
            params = list(inspect.signature(member).parameters.values())[1:]  # drop self
            for p in params:
                if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
                    offenders.append(f"{port.__name__}.{name}({p.name})")
    assert not offenders, f"port params must be keyword-only: {offenders}"


def test_adapter_health_is_typed_not_a_bare_dict():
    h = AdapterHealth(platform="signal", connected=True, last_event_age_seconds=None)
    assert h.connected is True
