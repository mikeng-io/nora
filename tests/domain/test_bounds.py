"""`over_fetch`/`cut` — the bit that says a bound actually bit.

The interesting cases are all about the BOUNDARY, because the whole mechanism
is one comparison. A read that returned exactly `limit` rows and a read that
stopped at `limit` with more behind it must produce different counts, and that
distinction is the entire product; everything else here defends it.
"""

from __future__ import annotations

from nora.domain.bounds import BOUND_SITES, TRUNCATED_METRIC, cut, over_fetch

SITE = next(iter(sorted(BOUND_SITES)))


class _Metrics:
    """Records name + labels, and — unlike a fake that accepts anything — is
    only used here to read back what was emitted. The declaredness of the name
    is the real `Metrics`' job and is covered by
    `tests/architecture/test_every_recorded_metric_is_declared.py`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[tuple[str, object], ...], float]] = []

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        self.calls.append((name, tuple(sorted(labels.items())), value))


def test_over_fetch_asks_for_exactly_one_more_row() -> None:
    # One, not two and not a percentage: the extra row's EXISTENCE is the
    # answer, so a second one would be paid for and never read.
    assert over_fetch(8) == 9
    assert over_fetch(1) == 2


def test_a_full_page_with_nothing_behind_it_is_not_a_truncation() -> None:
    """THE CASE THAT MAKES THE COUNTER MEAN ANYTHING. `len(rows) == limit`
    after over-fetching means the database had exactly `limit` rows — the cap
    did not bite. A counter that fired here would be measuring "the page was
    full", which is true of almost every healthy read."""
    metrics = _Metrics()
    kept = cut(list(range(8)), limit=8, site=SITE, metrics=metrics)
    assert kept == tuple(range(8))
    assert metrics.calls == []


def test_one_row_past_the_limit_counts_once_and_is_not_returned() -> None:
    metrics = _Metrics()
    kept = cut(list(range(9)), limit=8, site=SITE, metrics=metrics)
    assert kept == tuple(range(8)), "the over-fetched row must never escape"
    assert metrics.calls == [(TRUNCATED_METRIC, (("site", SITE),), 1.0)]


def test_the_count_is_a_bit_not_a_magnitude() -> None:
    """Deliberate: over-fetching by one cannot know the remainder, so a site
    that emitted `len(rows) - limit` would be reporting «1 more» when the truth
    might be «500 more» — a number that looks like a depth and is not one."""
    metrics = _Metrics()
    cut(list(range(9)), limit=8, site=SITE, metrics=metrics)
    assert metrics.calls[0][2] == 1.0


def test_a_short_read_returns_everything_and_counts_nothing() -> None:
    metrics = _Metrics()
    assert cut([1, 2], limit=8, site=SITE, metrics=metrics) == (1, 2)
    assert metrics.calls == []


def test_no_metrics_collaborator_still_cuts() -> None:
    """The stores take `metrics` optionally, and a test or offline composition
    that passes none must still get the RIGHT ROWS — the trim is the contract,
    the counting is the instrument."""
    assert cut(list(range(9)), limit=8, site=SITE, metrics=None) == tuple(range(8))


def test_an_undeclared_site_still_records_rather_than_raising(caplog) -> None:
    """A mistyped label must not take a read path down — `nora/language` and
    `nora/agent` are outside `make gate`'s mypy scope, so this check has to
    exist at runtime, and it has to fail SOFT. The wrong-label series lands
    where the right one would have, and the log line says which."""
    metrics = _Metrics()
    with caplog.at_level("WARNING"):
        kept = cut(list(range(3)), limit=2, site="nope.nothere", metrics=metrics)
    assert kept == (0, 1)
    assert metrics.calls == [(TRUNCATED_METRIC, (("site", "nope.nothere"),), 1.0)]
    assert "nope.nothere" in caplog.text


def test_every_declared_site_is_a_store_dot_method_name() -> None:
    """Named for where the bound is APPLIED, never for the caller — that is
    what lets two callers of one bounded read share a single series instead of
    multiplying cardinality to buy a distinction the store cannot make."""
    for site in BOUND_SITES:
        assert site.count(".") == 1, site
        store, method = site.split(".")
        assert store and method and not site.startswith("nora."), site


def test_a_non_positive_limit_returns_nothing_and_counts_nothing() -> None:
    """The guard lives in the HELPER, not in each caller.

    `0 > -1` is true, so an unguarded negative limit fabricates a truncation in
    the one series whose whole job is separating «there is nothing» from «I
    could not see all of it» — a fail-OPEN in the measure verb. And `rows[:-1]`
    silently drops a row, which is the `list[:-1]` trap the wired callers'
    docstrings cite as the reason for their own short-circuits.

    It is asserted here because this helper is meant to be wired to many
    callers, and an invariant re-implemented by every caller is the shape that
    produces the defect this module is about.
    """
    metrics = _Metrics()
    assert cut([1, 2, 3], limit=0, site=SITE, metrics=metrics) == ()
    assert cut([1, 2, 3], limit=-1, site=SITE, metrics=metrics) == ()
    assert cut([], limit=-1, site=SITE, metrics=metrics) == ()
    assert metrics.calls == [], "a bound that was never applied did not bite"
