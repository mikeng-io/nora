"""Ports are structural contracts. These tests pin the shape later phases build against."""

import inspect

from nora.domain import ports
from nora.domain.ports import (
    AdapterHealth,
    EngageDecision,
    MediaDescriptionStore,
    PlatformIdentity,
    SendResult,
)


def _all_ports() -> list[type]:
    """Every Protocol *defined in* nora.domain.ports — enumerated, not hardcoded, so
    a sixth port added later is automatically covered by the tests below instead of
    being silently exempt until someone remembers to add its name to a list.

    Filters on `__module__` as well as `_is_protocol`: `vars(ports)` also picks up
    `typing.Protocol` itself (imported at module scope for the `class X(Protocol)`
    declarations below it) — and `typing.Protocol` reports `_is_protocol = True`,
    so the module check is what excludes it from the results.
    """
    return [
        member for member in vars(ports).values()
        if isinstance(member, type)
        and getattr(member, "_is_protocol", False)
        and member.__module__ == ports.__name__
    ]


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
    """The classifier's reason must reach the prefill, and a bare confidence is
    uninterpretable without the threshold it was compared against."""
    d = EngageDecision(engage=True, reason="trigger_text")
    assert d.engage and d.rationale is None and d.confidence is None
    c = EngageDecision(engage=True, reason="classifier",
                       rationale="asked a factual question about dinner",
                       confidence=0.82, threshold=0.7, allow_episode=True)
    assert c.confidence > c.threshold and c.allow_episode


def test_ports_are_runtime_checkable_protocols():
    for port in _all_ports():
        assert getattr(port, "_is_protocol", False), f"{port.__name__} is not a Protocol"
        assert getattr(port, "_is_runtime_protocol", False), (
            f"{port.__name__} is not @runtime_checkable"
        )


def test_every_port_is_enumerated():
    """A sanity check on `_all_ports()` itself: it must find at least the five ports
    named explicitly elsewhere in this file, so a bug in the enumeration (e.g. an
    import that shadows `_is_protocol`) fails loudly instead of silently covering
    fewer ports than intended."""
    found = {p.__name__ for p in _all_ports()}
    expected = {"PlatformAdapter", "RecordStore", "MemoryStore", "EngagePolicy", "ContextAssembler"}
    assert expected <= found, f"expected ports missing from enumeration: {expected - found}"


def test_port_methods_are_keyword_only_after_self():
    """Convention: free extensibility forever. A positional parameter
    added to a port later is a breaking change at every call site.

    Checks every Protocol in nora.domain.ports (enumerated by `_all_ports()`, not a
    hardcoded list) so a sixth port isn't silently exempt. Flags both
    POSITIONAL_OR_KEYWORD and POSITIONAL_ONLY params: a `self, x, /` parameter is
    just as much a breaking-change trap as a plain positional-or-keyword one, and
    checking only the former would let it slip through unnoticed.
    """
    offenders = []
    for port in _all_ports():
        for name, member in vars(port).items():
            if name.startswith("_") or not callable(member):
                continue
            params = list(inspect.signature(member).parameters.values())[1:]  # drop self
            for p in params:
                if p.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.POSITIONAL_ONLY,
                ):
                    offenders.append(f"{port.__name__}.{name}({p.name})")
    assert not offenders, f"port params must be keyword-only: {offenders}"


def test_adapter_health_is_typed_not_a_bare_dict():
    h = AdapterHealth(platform="signal", connected=True, last_event_age_seconds=None)
    assert h.connected is True


def test_media_description_store_methods_are_keyword_only():
    """This file's standing convention: a positional parameter added to a port
    later is a breaking change at every call site."""
    for name in ("resolve", "describe", "remember"):
        sig = inspect.signature(getattr(MediaDescriptionStore, name))
        positional = [
            p for p in sig.parameters.values()
            if p.name != "self" and p.kind is not p.KEYWORD_ONLY
        ]
        assert positional == [], f"{name} takes a positional parameter"
