"""`LiveSet` is a `collections.abc.Set`, so `-`, `&`, `|` and `^` with it run
`Set._from_iterable`, which builds the result as `cls(iterable)` — on the bare
class that is a LiveSet whose `_read` is the consumed generator, and its first
use raises 'generator' object is not callable."""

from nora.domain.live import LiveSet


def test_set_algebra_on_a_live_set_stays_iterable() -> None:
    opted_out = LiveSet(lambda: {"cy", "cy_old"})
    assert sorted({"cy", "mike", "nora"} - opted_out) == ["mike", "nora"]
    assert sorted({"cy", "mike"} | opted_out) == ["cy", "cy_old", "mike"]
    assert sorted(opted_out - {"cy"}) == ["cy_old"]
    assert sorted(opted_out & {"cy", "nora"}) == ["cy"]
