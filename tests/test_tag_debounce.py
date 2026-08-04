from nora.models import ConversationTarget
from nora.tag import Debounce


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


A = ConversationTarget("signal", "gA", "group")
B = ConversationTarget("signal", "gB", "group")


def test_first_tag_is_allowed():
    assert Debounce(5, clock=FakeClock()).allow(A) is True


def test_second_tag_within_the_window_is_blocked():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    clock.advance(1)
    assert d.allow(A) is False


def test_tag_after_the_window_is_allowed():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    clock.advance(5.001)
    assert d.allow(A) is True


def test_the_window_is_independent_per_conversation():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    assert d.allow(B) is True
    clock.advance(1)
    assert d.allow(A) is False
    assert d.allow(B) is False


def test_a_blocked_tag_does_not_extend_the_window():
    """Otherwise a burst of tags would starve the conversation indefinitely."""
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(4)
    assert d.allow(A) is False
    clock.advance(1.001)
    assert d.allow(A) is True


def test_zero_interval_allows_everything():
    clock = FakeClock()
    d = Debounce(0, clock=clock)
    assert d.allow(A) is True
    assert d.allow(A) is True


def test_a_platform_conversation_tuple_works_as_a_key():
    d = Debounce(5, clock=FakeClock())
    assert d.allow(("signal", "gA")) is True
    assert d.allow(("signal", "gA")) is False


def test_sweep_drops_entries_older_than_the_ttl():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(100)
    dropped = d.sweep(10)
    assert dropped == 1
    assert len(d._last) == 0


def test_sweep_keeps_entries_within_the_ttl():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(1)
    dropped = d.sweep(10)
    assert dropped == 0
    assert len(d._last) == 1


def test_sweep_only_drops_the_stale_key_not_the_fresh_one():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(100)
    d.allow(B)
    dropped = d.sweep(10)
    assert dropped == 1
    assert list(d._last) == [B]


def test_sweep_returns_zero_on_an_empty_debounce():
    assert Debounce(5, clock=FakeClock()).sweep(10) == 0
