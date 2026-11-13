"""The wake's loop over the cognitive graph, counted: distinct keys memory
reads returned, of those how many the session had already been shown
(prefetch included), the graph links the diff wrote or revised, and how many
of the previous wake's links this wake's recorded prefetch carried. What the
trajectory cannot ground is `None` — unavailable — never zero."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from scripts.cognition_loop.trajectory import loop_counts, prefetch_refs

_AGENDA = (
    "you wake to an agenda\n"
    '<graph order="60" key="turn">\n'
    "@person:p-ada Ada\n"
    "@unit:u the unit\n"
    "r-aaa: person:p-ada ─about─▶ profile_line:l-1\n"
    "</graph>\n"
    "open items follow"
)


def _read(keys: list[list[str]], tool: str = "memory_search") -> dict[str, Any]:
    return {
        "tool": tool,
        "status": "ok",
        "result": {"content": [{"json": {"status": "ok", "keys": keys}}]},
    }


def _diff(*ids: str) -> Any:
    return SimpleNamespace(
        kinds={
            "graph": SimpleNamespace(
                created=[SimpleNamespace(after={"id": i}) for i in ids],
                revised=[], retired=[], unchanged=[],
            )
        }
    )


def test_prefetch_refs_reads_the_recorded_graph_block():
    keys, relations = prefetch_refs(_AGENDA)
    assert ("person", "p-ada") in keys and ("unit", "u") in keys
    assert relations == {"r-aaa"}
    assert prefetch_refs("no graph block") is None
    assert prefetch_refs(None) is None


def test_discovered_repeats_and_carried_links():
    trajectory = {
        "blocks": {"agenda": _AGENDA},
        "tool_calls": [
            _read([["person", "p-ada"], ["note", "n-1"]]),
            _read([["note", "n-1"], ["note", "n-2"]]),
        ],
    }
    loop = loop_counts(trajectory, _diff("r-bbb", "r-ccc"), {"r-aaa", "r-zzz"})
    assert loop == {
        "discovered": 3,
        # p-ada came from prefetch; n-1 was already shown by the first read.
        "repeats": 2,
        "links_written": 2,
        "links_carried": 1,
        "link_ids": ["r-bbb", "r-ccc"],
    }


def test_an_unrecorded_prefetch_reports_unavailable_never_zero():
    trajectory = {
        "blocks": {"agenda": "no graph block"},
        "tool_calls": [_read([["note", "n-1"]])],
    }
    loop = loop_counts(trajectory, _diff(), {"r-aaa"})
    assert loop["discovered"] == 1
    assert loop["repeats"] is None
    assert loop["links_carried"] is None


def test_an_unavailable_read_counts_nothing():
    trajectory = {
        "blocks": {"agenda": _AGENDA},
        "tool_calls": [
            {
                "tool": "memory_read",
                "status": "unavailable",
                "result": {"content": [{"json": {"status": "unavailable"}}]},
            }
        ],
    }
    loop = loop_counts(trajectory, _diff(), set())
    assert loop["discovered"] == 0
    assert loop["repeats"] == 0
    assert loop["links_written"] == 0
    assert loop["links_carried"] == 0


def test_a_relation_endpoint_shown_in_the_prefetch_counts_as_a_repeat():
    """An emitted `rid: src ─rel─▶ dst` row shows BOTH endpoints, even when
    the endpoint's own node row never fit the budget (`l-1` has no
    `@profile_line` row in `_AGENDA`): a later read of one is a repeat."""
    trajectory = {
        "blocks": {"agenda": _AGENDA},
        "tool_calls": [_read([["profile_line", "l-1"], ["note", "n-1"]])],
    }
    loop = loop_counts(trajectory, _diff(), set())
    assert loop["repeats"] == 1
    assert loop["discovered"] == 2


def test_the_recorded_prefetch_provenance_counts_a_later_read():
    """`prefetch` is the renderer's canonical provenance the session recorded
    (`nora.session.started`'s `graph_keys`/`graph_relations`), preferred over
    re-parsing the block: it grounds repeats and carried links where the
    trajectory holds no `<graph>` text at all."""
    trajectory = {
        "blocks": {"agenda": "no graph block"},
        "prefetch": {
            "keys": [["profile_line", "l-1"]],
            "relations": ["r-aaa"],
        },
        "tool_calls": [
            _read([["profile_line", "l-1"]]),
            _read([["note", "n-1"]]),
        ],
    }
    loop = loop_counts(trajectory, _diff(), {"r-aaa"})
    assert loop["repeats"] == 1
    assert loop["links_carried"] == 1
