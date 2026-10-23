"""`ToolRegistry.tool_catalogue()` — the index the model selects from.

The catalogue is the selection half of tool loading: one line per
tool, every tool, resident in every prompt, so that the full schemas need not
be. These tests are the line discipline that keeps it an INDEX. Without them
it rots back into a second copy of the descriptions, which is precisely what
DEC-150 deleted the generated capability block for.
"""

from __future__ import annotations

import tiktoken

from nora.agent.registry import ToolRegistry
from nora.agent.surface import DREAMER_ONLY_TOOLS, ToolScope
from nora.agent.tools import HOT_TOOLS, LOADER_TOOL, NATIVE_TOOLS, on_memory_surface

ENC = tiktoken.get_encoding("cl100k_base")

# The block ceiling, in tokens, asserted over the LARGEST PER-PLATFORM tag
# catalogue (`_tag_catalogue`: the registered tools `ToolScope` admits for a
# tag turn, every optional tool wired).
#
# The catalogue is paid on EVERY call of EVERY turn, so aggregate growth is
# what turns a small index back into a full schema dump. Each line is ADR
# 0007's L1 paragraph (what + WHEN), pinned by
# `test_the_opening_paragraph_is_an_l1`. The ceiling is deliberately not sized
# to the current value: it leaves headroom over the real
# block. Raise it only for a new tool, from the MEASURED block and with the
# tool's own line count, never for a line rewritten to fit; `MEAN_CEILING`
# below binds per-line drift.
#
# Dreamer-only tools (`DREAMER_LOOP_TOOLS`) are kept off every tag turn's tools
# and catalogue by `ToolScope.admits`, so a tag turn pays NOTHING for their
# lines; they are counted only by `ToolRegistry.tool_catalogue()`, which lists
# every registered tool (`test_every_registered_tool_has_a_catalogue_line`)
# and is not what any turn is shown. Tools that ship withheld
# (`[documents] enabled = false`, `[video_gen] enabled = false`) cost a live
# turn nothing until they are turned on.
BLOCK_CEILING = 2_600

#: The platforms a tag turn runs on. The catalogue differs only by the tools
#: scoped to one (`DISCORD_ONLY_TOOLS`) and by the Dreamer-only ones, which no
#: tag turn admits.
TAG_PLATFORMS = ("signal", "discord", "whatsapp", "instagram")

# The per-line ceiling. Lines carry the tokens that discriminate between
# neighbours (`search_memory` «in this group's history» against
# `search_memory_workspace` «across EVERY room»; `create_event` «a NEW
# gathering» against `update_event` «ALREADY proposed»), so cutting them to a
# tighter number would remove the reason the line exists.
#
# So this is set where it catches the failure it is FOR — a line that has
# become a description — rather than where it forces cosmetic surgery on lines
# doing real work. A description is 100+ tokens; nothing legitimate is near 40.
# The block ceiling and the mean below are what bind ordinary drift.
LINE_CEILING = 40
# A when-clause costs ~8-12 tokens and every line carries one.
MEAN_CEILING = 33


def _lines() -> list[str]:
    return ToolRegistry().tool_catalogue().splitlines()


def _tag_catalogue(platform: str, *, graph: bool = False) -> list[str]:
    """The catalogue lines a TAG turn on `platform` is shown: every registered
    tool its scope admits, never a Dreamer-only one, on ONE memory surface
    (DEC-316: `graph` is `[memory_index] enabled`)."""
    scope = ToolScope(platform=platform, dreamer=False)
    return [
        line for line in _lines()
        if scope.admits(name := line.split(" — ")[0][2:])
        and on_memory_surface(name, graph=graph)
    ]


def test_every_registered_tool_has_a_catalogue_line():
    """The catalogue is the ONLY thing telling the model a tool exists once the
    schemas are deferred. A tool missing from it is a capability that silently
    cannot be reached — DEC-28's failure, where a model was read as refusing to
    use a tool it had never been shown."""
    named = {line.split(" — ")[0][2:] for line in _lines()}
    # `load_tools` is plumbing, not a capability: the block header says how to
    # load, and a line for the loader would be a line spent on itself.
    expected = {
        name for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin and name != LOADER_TOOL
    }
    assert named == expected


def test_every_line_carries_a_description():
    """A bare name discriminates nothing. If a tool's docstring cannot produce
    a first sentence, the catalogue would ship its name alone and the model
    would have to guess what it does."""
    assert [line for line in _lines() if " — " not in line] == []


def test_the_catalogue_stays_an_index_and_not_a_second_copy_of_the_schemas():
    """Gate 2a, over what a tag turn pays: the LARGEST per-platform catalogue.
    Re-pointing this at a larger number instead of fixing the lines is how the
    block becomes the thing it replaced."""
    sizes = {
        f"{p}{' (graph)' if graph else ''}": len(
            ENC.encode("\n".join(_tag_catalogue(p, graph=graph))))
        for p in TAG_PLATFORMS for graph in (False, True)
    }
    platform, total = max(sizes.items(), key=lambda kv: kv[1])
    assert total <= BLOCK_CEILING, (
        f"the {platform} tag catalogue is {total} tokens, ceiling {BLOCK_CEILING} "
        f"({sizes}). Lines are being written as descriptions — fix the lines, not "
        "the ceiling."
    )


def test_a_tag_catalogue_holds_no_dreamer_only_line():
    """The ceiling is measured over a tag turn's lines, so a Dreamer-only tool
    must not be among them: if it were, the block would be paid where it is not
    meant to be, and the comment above would be true after all."""
    for platform in TAG_PLATFORMS:
        named = {line.split(" — ")[0][2:] for line in _tag_catalogue(platform)}
        assert not named & DREAMER_ONLY_TOOLS, (platform, named & DREAMER_ONLY_TOOLS)
    assert len(_tag_catalogue("discord")) > len(_tag_catalogue("signal"))


def test_no_single_line_has_become_a_description():
    lines = _lines()
    over = [(len(ENC.encode(line)), line) for line in lines]
    worst = max(over)
    assert worst[0] <= LINE_CEILING, f"{worst[1]!r} is {worst[0]} tokens"

    mean = sum(n for n, _ in over) / len(over)
    assert mean <= MEAN_CEILING, (
        f"mean line is {mean:.1f} tokens. No single line tripped, so this is "
        "drift across many of them — the shape the per-line gate cannot see."
    )


def test_confusable_pairs_are_discriminated_in_the_catalogue_itself():
    """The whole risk of a one-line catalogue is selection between neighbours,
    so the neighbours are pinned HERE rather than left to a model eval.

    `where_is`/`where_been` is the pair most easily confused: unless `where_is`
    says RIGHT NOW, both lines read as questions about the past. The other three
    discriminate already; they are pinned so a docstring edit cannot quietly
    remove the distinction.
    """
    by_name = {line.split(" — ")[0][2:]: line for line in _lines()}

    assert "RIGHT NOW" in by_name["where_is"]
    assert "HAS BEEN" in by_name["where_been"]

    assert "named" in by_name["parking_status"]
    assert "nearest" in by_name["find_parking"]

    assert "this group's" in by_name["search_memory"]
    assert "EVERY room" in by_name["search_memory_workspace"]

    assert "NEW" in by_name["event_propose"]
    assert "ALREADY" in by_name["event_revise"]

    assert "history" in by_name["version_list"]
    assert "upgrades" in by_name["version_list"]
    assert "names a version" in by_name["version_read"]
    read = " ".join(ToolRegistry()._descriptions()["version_read"].split())
    assert "without version_list before or after" in read


def test_the_hot_set_is_marked_and_matches_the_one_declaration():
    """A marker saying «already loaded» while the builder did not load it sends
    the model to fetch something it holds, or worse, stops it fetching
    something it does not. The marker and the builder read the same
    `HOT_TOOLS`, and this is what stops a second list appearing."""
    marked = {line.split(" — ")[0][2:] for line in _lines() if line.startswith("*")}
    assert marked == set(HOT_TOOLS)


def test_send_reply_is_resident_whatever_its_usage_share():
    """Structural, not measured. `send_reply` is the terminal verb — the turn
    ends by `ReplyGuard` observing it (DEC-21/DEC-32) — so a turn that had to
    fetch it before it could reply is a turn that can fail to end. Re-deriving
    the hot set from fresh call counts must never drop it."""
    assert "send_reply" in HOT_TOOLS


def test_the_catalogue_and_the_gate_digest_render_the_same_way():
    """Two renderings of one thing is the drift DEC-150 deleted the capability
    block for. `capability_digest` (the Relevance Gate's read-only view) and
    `tool_catalogue` (the model's full view) share `_digest_lines`, so a tool
    in both must render identically apart from the resident marker."""
    registry = ToolRegistry()
    digest = {
        line.split(" — ")[0][2:]: line.split(" — ", 1)[-1]
        for line in registry.capability_digest().splitlines()
    }
    catalogue = {
        line.split(" — ")[0][2:]: line.split(" — ", 1)[-1]
        for line in registry.tool_catalogue().splitlines()
    }
    shared = set(digest) & set(catalogue)
    assert shared, "the two renderings share no tools — one of them is empty"
    assert {name: digest[name] for name in shared} == {
        name: catalogue[name] for name in shared
    }


def test_the_catalogue_covers_more_than_the_gate_digest():
    """They are scoped to different readers and must not collapse into one.
    The gate asks «could an answer exist», so it sees read-only tools; the
    model is about to choose, so it sees the act verbs too."""
    registry = ToolRegistry()
    assert len(registry.tool_catalogue().splitlines()) > len(
        registry.capability_digest().splitlines()
    )
