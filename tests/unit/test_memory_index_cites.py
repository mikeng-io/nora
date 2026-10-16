"""How a stored citation becomes a `cites` edge in the memory index: pure, no
database."""

from __future__ import annotations

from nora.infra.sql import memory_index


def test_a_cited_rollup_key_of_an_episode_kind_is_a_cites_edge() -> None:
    """`rollup(outing|...)` points at the outing's key, as cited; a kind
    with no node (a daybook) is skipped, a message id stays a message."""
    key = "friends|a,b|2026-09-21T19:00:00+08:00/2026-09-21T22:00:00+08:00"
    got = memory_index._cites(
        "memo", "m1",
        ["12", f"rollup(outing|{key})", "rollup(stay|a|2026-09-21T19:00:00+08:00/"
         "2026-09-21T20:00:00+08:00)", "rollup(daybook|friends|a|x/y)", "response:4"],
        "memo_revision.citations",
    )
    assert [(e[3], e[4]) for e in got] == [
        ("message", "12"), ("outing", key),
        ("stay", "a|2026-09-21T19:00:00+08:00/2026-09-21T20:00:00+08:00"),
    ]
